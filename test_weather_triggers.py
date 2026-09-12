"""The weather trigger vocabulary: the cap, the month ordering, and what
evaluate_trigger() says when a snapshot can and cannot answer a rule.

    python3 test_weather_triggers.py

No pytest, no new dependencies, no disk or network -- this module is pure
data and arithmetic.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import weather_triggers as wt                    # noqa: E402


section("The vocabulary")
check("six verticals", set(wt.VERTICALS),
     {"restaurant", "hvac", "retail", "auto", "landscaping", "pool_spa"})
check("fourteen restaurant triggers", len(wt.triggers_for_vertical("restaurant")), 14)
check("thirteen hvac triggers", len(wt.triggers_for_vertical("hvac")), 13)
check("thirteen retail triggers", len(wt.triggers_for_vertical("retail")), 13)
check("thirteen auto triggers", len(wt.triggers_for_vertical("auto")), 13)
check("thirteen landscaping triggers", len(wt.triggers_for_vertical("landscaping")), 13)
check("thirteen pool_spa triggers", len(wt.triggers_for_vertical("pool_spa")), 13)
check("every trigger is one of the six verticals",
     all(t.vertical in wt.VERTICALS for t in wt.TRIGGERS.values()), True)
check("no trigger id is shared across verticals",
     len(wt.TRIGGERS), sum(len(wt.triggers_for_vertical(v)) for v in wt.VERTICALS))
check("cap is three", wt.MAX_TRIGGERS, 3)
for vertical in wt.VERTICALS:
    check(f"every month has every {vertical} trigger", all(
        set(wt.month_order(vertical, m)) == set(wt.triggers_for_vertical(vertical))
        for m in wt.MONTH_ABBR), True)
check("spec's Sep example order is honored (first four)",
     wt.month_order("restaurant", "Sep")[:4],
     ["patio-day", "first-cool-night", "rain-delay", "crisp-day"])
check("hvac's Jul order leads with the emergency-cooling pair",
     wt.month_order("hvac", "Jul")[:3],
     ["ac-overload", "heat-index-strain", "storm-power-risk"])
check("retail's Jul order leads with the cooling stock-up pair",
     wt.month_order("retail", "Jul")[:3],
     ["heat-wave-cooling", "heat-index-retail", "storm-prep"])
check("auto's Jul order leads with the cabin-AC pair",
     wt.month_order("auto", "Jul")[:3],
     ["heat-index-auto", "ac-check-early", "storm-driving-prep"])
check("landscaping's Jul order leads with the drought pair",
     wt.month_order("landscaping", "Jul")[:3],
     ["dry-spell-watering", "drought-stress", "heat-wave-lawn-stress"])
check("pool_spa's Jul order leads with the chlorine/algae pair",
     wt.month_order("pool_spa", "Jul")[:3],
     ["chlorine-burn-off", "algae-bloom-risk", "evaporation-watch"])
check("unknown month falls back to the registry order, restaurant",
     set(wt.month_order("restaurant", "Nope")), set(wt.triggers_for_vertical("restaurant")))
check("unknown month falls back to the registry order, hvac",
     set(wt.month_order("hvac", "Nope")), set(wt.triggers_for_vertical("hvac")))
check("unknown month falls back to the registry order, retail",
     set(wt.month_order("retail", "Nope")), set(wt.triggers_for_vertical("retail")))
check("unknown month falls back to the registry order, auto",
     set(wt.month_order("auto", "Nope")), set(wt.triggers_for_vertical("auto")))
check("hvac's month order never leaks restaurant's ids",
     bool(set(wt.month_order("hvac", "Jan")) & set(wt.triggers_for_vertical("restaurant"))), False)
check("retail's month order never leaks either other vertical's ids",
     bool(set(wt.month_order("retail", "Jan")) &
         (set(wt.triggers_for_vertical("restaurant")) | set(wt.triggers_for_vertical("hvac")))),
     False)
check("auto's month order never leaks any other vertical's ids",
     bool(set(wt.month_order("auto", "Jan")) &
         (set(wt.triggers_for_vertical("restaurant")) | set(wt.triggers_for_vertical("hvac")) |
          set(wt.triggers_for_vertical("retail")))),
     False)
check("unknown month falls back to the registry order, landscaping",
     set(wt.month_order("landscaping", "Nope")), set(wt.triggers_for_vertical("landscaping")))
check("unknown month falls back to the registry order, pool_spa",
     set(wt.month_order("pool_spa", "Nope")), set(wt.triggers_for_vertical("pool_spa")))
_OTHER_FOUR = (set(wt.triggers_for_vertical("restaurant")) | set(wt.triggers_for_vertical("hvac")) |
              set(wt.triggers_for_vertical("retail")) | set(wt.triggers_for_vertical("auto")))
check("landscaping's month order never leaks any other vertical's ids",
     bool(set(wt.month_order("landscaping", "Jan")) & _OTHER_FOUR), False)
check("pool_spa's month order never leaks any other vertical's ids",
     bool(set(wt.month_order("pool_spa", "Jan")) &
         (_OTHER_FOUR | set(wt.triggers_for_vertical("landscaping")))),
     False)


section("The cap is enforced server-side, not only in the picker")
ok, err = wt.validate_picks(["patio-day", "cold-snap"])
check("two picks: ok", ok, True)
ok, err = wt.validate_picks(["patio-day", "cold-snap", "snow-day", "wind-chill"])
check("four picks: refused", ok, False)
check("four picks: names the cap", "3" in err, True)
ok, err = wt.validate_picks(["patio-day", "patio-day"])
check("duplicate pick: refused", ok, False)
ok, err = wt.validate_picks(["not-a-real-trigger"])
check("unknown trigger id: refused", ok, False)
ok, err = wt.validate_picks([])
check("empty picks: refused", ok, False)
ok, err = wt.validate_picks(["patio-day", "cold-snap", "snow-day"])
check("exactly the cap: ok", ok, True)
ok, err = wt.validate_picks(["ac-overload", "hard-freeze"], "hvac")
check("two hvac picks: ok", ok, True)
ok, err = wt.validate_picks(["ac-overload", "patio-day"], "hvac")
check("a restaurant id is not a recognized hvac trigger", ok, False)
ok, err = wt.validate_picks(["patio-day", "cold-snap"], "hvac")
check("both restaurant ids refused under the hvac vertical", ok, False)
ok, err = wt.validate_picks(["ac-overload", "cold-snap"])
check("an hvac id is not a recognized restaurant trigger", ok, False)
ok, err = wt.validate_picks(["storm-prep", "heat-wave-cooling"], "retail")
check("two retail picks: ok", ok, True)
ok, err = wt.validate_picks(["storm-prep", "patio-day"], "retail")
check("a restaurant id is not a recognized retail trigger", ok, False)
ok, err = wt.validate_picks(["storm-prep", "ac-overload"], "retail")
check("an hvac id is not a recognized retail trigger", ok, False)
ok, err = wt.validate_picks(["heat-wave-cooling", "patio-day"])
check("a retail id is not a recognized restaurant trigger", ok, False)
ok, err = wt.validate_picks(["storm-driving-prep", "battery-cold-test"], "auto")
check("two auto picks: ok", ok, True)
ok, err = wt.validate_picks(["storm-driving-prep", "patio-day"], "auto")
check("a restaurant id is not a recognized auto trigger", ok, False)
ok, err = wt.validate_picks(["storm-driving-prep", "ac-overload"], "auto")
check("an hvac id is not a recognized auto trigger", ok, False)
ok, err = wt.validate_picks(["storm-driving-prep", "heat-wave-cooling"], "auto")
check("a retail id is not a recognized auto trigger", ok, False)
ok, err = wt.validate_picks(["battery-cold-test", "patio-day"])
check("an auto id is not a recognized restaurant trigger", ok, False)
ok, err = wt.validate_picks(["storm-cleanup", "spring-green-up-day"], "landscaping")
check("two landscaping picks: ok", ok, True)
ok, err = wt.validate_picks(["storm-cleanup", "patio-day"], "landscaping")
check("a restaurant id is not a recognized landscaping trigger", ok, False)
ok, err = wt.validate_picks(["storm-cleanup", "battery-cold-test"], "landscaping")
check("an auto id is not a recognized landscaping trigger", ok, False)
ok, err = wt.validate_picks(["spring-green-up-day", "patio-day"])
check("a landscaping id is not a recognized restaurant trigger", ok, False)
ok, err = wt.validate_picks(["storm-debris-cleanup", "pool-opening-day"], "pool_spa")
check("two pool_spa picks: ok", ok, True)
ok, err = wt.validate_picks(["storm-debris-cleanup", "patio-day"], "pool_spa")
check("a restaurant id is not a recognized pool_spa trigger", ok, False)
ok, err = wt.validate_picks(["storm-debris-cleanup", "spring-green-up-day"], "pool_spa")
check("a landscaping id is not a recognized pool_spa trigger", ok, False)
ok, err = wt.validate_picks(["pool-opening-day", "patio-day"])
check("a pool_spa id is not a recognized restaurant trigger", ok, False)


section("Plain range rules")
snap = {"temperature": 74, "rain_probability": 0, "wind_mph": 8}
r = wt.evaluate_trigger("patio-day", snap)
check("patio-day matches a calm 74F dry day", r["active"], True)
check("patio-day is measured", r["measured"], True)

snap_hot = {"temperature": 90, "rain_probability": 0, "wind_mph": 8}
r = wt.evaluate_trigger("patio-day", snap_hot)
check("patio-day refuses a 90F day", r["active"], False)

r = wt.evaluate_trigger("patio-day", {"temperature": 74})
check("patio-day with no rain/wind data is not measured", r["measured"], False)
check("not measured means active is None", r["active"], None)

r = wt.evaluate_trigger("cold-snap", {"temperature": 20})
check("cold-snap fires under 32F", r["active"], True)
r = wt.evaluate_trigger("cold-snap", {"temperature": 40})
check("cold-snap does not fire at 40F", r["active"], False)

r = wt.evaluate_trigger("snow-day", {"snow_inches": 3})
check("snow-day fires at 3in", r["active"], True)
r = wt.evaluate_trigger("snow-day", {"snow_inches": 0.5})
check("snow-day does not fire at 0.5in", r["active"], False)


section("Heat index — the NOAA regression, not the bare temperature")
r = wt.evaluate_trigger("heat-index", {"temperature": 95, "humidity": 70})
check("heat-index fires on hot + humid", r["active"], True)
r = wt.evaluate_trigger("heat-index", {"temperature": 95, "humidity": 10})
check("heat-index does not fire on hot + dry", r["active"], False)
r = wt.evaluate_trigger("heat-index", {"temperature": 95})
check("heat-index with no humidity is not measured", r["measured"], False)


section("Evening cooldown — approximated against today's high")
r = wt.evaluate_trigger("evening-cooldown", {"forecast_high": 90, "temperature": 78},
                        now_hhmm="19:00")
check("evening-cooldown fires on a 12-degree drop", r["active"], True)
r = wt.evaluate_trigger("evening-cooldown", {"forecast_high": 90, "temperature": 88},
                        now_hhmm="19:00")
check("evening-cooldown does not fire on a 2-degree drop", r["active"], False)
r = wt.evaluate_trigger("evening-cooldown", {"forecast_high": 90, "temperature": 78},
                        now_hhmm="10:00")
check("evening-cooldown outside its window never fires", r["active"], False)
check("outside the window is still measured (not a data gap)", r["measured"], True)


section("Storm watch — an alert, never a probability")
r = wt.evaluate_trigger("storm-watch", {"official_alerts": ["Severe Thunderstorm Warning"]})
check("storm-watch fires on a real alert", r["active"], True)
r = wt.evaluate_trigger("storm-watch", {"official_alerts": []})
check("storm-watch does not fire with no alert", r["active"], False)


section("Gray streak — a consecutive-day count, carried in state")
state = {}
for day_cloudy in (True, True, False, True, True, True):
    r = wt.evaluate_trigger("gray-streak", {"cloud_percent": 90 if day_cloudy else 20}, state)
    state = r["state"]
check("three-in-a-row after a reset fires on the third", r["active"], True)
r = wt.evaluate_trigger("gray-streak", {})
check("gray-streak with no cloud data is not measured", r["measured"], False)


section("Once-per-season triggers fire exactly once")
state = {}
r = wt.evaluate_trigger("first-freeze", {"forecast_low": 30}, state,
                        today=date(2026, 11, 10))
check("first-freeze fires the first time it is cold enough", r["active"], True)
state = r["state"]
r = wt.evaluate_trigger("first-freeze", {"forecast_low": 25}, state,
                        today=date(2026, 11, 15))
check("first-freeze does not fire again the same season", r["active"], False)
r = wt.evaluate_trigger("first-freeze", {"forecast_low": 20}, state,
                        today=date(2027, 11, 5))
check("first-freeze fires again the following season", r["active"], True)

state = {}
r = wt.evaluate_trigger("first-cool-night", {"forecast_low": 50}, state,
                        today=date(2026, 8, 1), now_hhmm="18:00")
check("first-cool-night refuses before its not_before date", r["active"], False)
r = wt.evaluate_trigger("first-cool-night", {"forecast_low": 50}, state,
                        today=date(2026, 8, 20), now_hhmm="18:00")
check("first-cool-night fires once its date has arrived", r["active"], True)


section("An unknown trigger never claims a verdict")
r = wt.evaluate_trigger("not-real", {})
check("unknown trigger id is not measured", r["measured"], False)
check("unknown trigger id has no verdict", r["active"], None)


section("HVAC — the second vertical, same rule vocabulary")
r = wt.evaluate_trigger("ac-overload", {"temperature": 95})
check("ac-overload fires above 92F", r["active"], True)
r = wt.evaluate_trigger("ac-overload", {"temperature": 88})
check("ac-overload does not fire at 88F", r["active"], False)

r = wt.evaluate_trigger("heat-index-strain", {"temperature": 96, "humidity": 60})
check("heat-index-strain fires on hot + humid", r["active"], True)
r = wt.evaluate_trigger("heat-index-strain", {"temperature": 96})
check("heat-index-strain with no humidity is not measured", r["measured"], False)

r = wt.evaluate_trigger("hard-freeze", {"temperature": 10})
check("hard-freeze fires at 10F", r["active"], True)
r = wt.evaluate_trigger("deep-freeze", {"temperature": 10})
check("deep-freeze does not fire at 10F -- it escalates past hard-freeze", r["active"], False)
r = wt.evaluate_trigger("deep-freeze", {"temperature": -5})
check("deep-freeze fires below zero", r["active"], True)

r = wt.evaluate_trigger("spring-tune-up-day",
                        {"temperature": 72, "rain_probability": 5},
                        today=date(2026, 4, 15))
check("spring-tune-up-day fires on a mild dry April day", r["active"], True)
r = wt.evaluate_trigger("spring-tune-up-day",
                        {"temperature": 72, "rain_probability": 5},
                        today=date(2026, 8, 15))
check("spring-tune-up-day is outside its months in August", r["active"], False)

r = wt.evaluate_trigger("fall-tune-up-day",
                        {"temperature": 60, "rain_probability": 10},
                        today=date(2026, 10, 1))
check("fall-tune-up-day fires on a mild dry October day", r["active"], True)

state = {}
r = wt.evaluate_trigger("first-hard-freeze", {"forecast_low": 25}, state,
                        today=date(2026, 11, 1))
check("first-hard-freeze fires the first time it is cold enough", r["active"], True)
r = wt.evaluate_trigger("first-hard-freeze", {"forecast_low": 20}, r["state"],
                        today=date(2026, 11, 10))
check("first-hard-freeze does not fire again the same season", r["active"], False)

r = wt.evaluate_trigger("storm-power-risk", {"official_alerts": ["Severe Thunderstorm Warning"]})
check("storm-power-risk fires on a real alert", r["active"], True)
r = wt.evaluate_trigger("storm-power-risk", {"official_alerts": []})
check("storm-power-risk does not fire with no alert", r["active"], False)

r = wt.evaluate_trigger("snow-load", {"snow_inches": 6})
check("snow-load fires at 6in", r["active"], True)
r = wt.evaluate_trigger("snow-load", {"snow_inches": 1})
check("snow-load does not fire at 1in", r["active"], False)

# The two once-per-season triggers -- restaurant's first-freeze and hvac's
# first-hard-freeze -- both key their season state on the rule's own
# once_per_season value ("cold"), not on the trigger id. They must not be
# able to see each other's carried state.
r1 = wt.evaluate_trigger("first-freeze", {"forecast_low": 20},
                         {"season_fired_cold_2026": "2026-11-01"},
                         today=date(2026, 11, 2))
check("first-freeze's own carried state still blocks a second fire", r1["active"], False)
r2 = wt.evaluate_trigger("first-hard-freeze", {"forecast_low": 20}, {},
                         today=date(2026, 11, 2))
check("first-hard-freeze has its own, separate state and fires", r2["active"], True)


section("Retail / Home Goods — the third vertical, same rule vocabulary")
r = wt.evaluate_trigger("heat-wave-cooling", {"temperature": 95})
check("heat-wave-cooling fires above 90F", r["active"], True)
r = wt.evaluate_trigger("heat-wave-cooling", {"temperature": 85})
check("heat-wave-cooling does not fire at 85F", r["active"], False)

r = wt.evaluate_trigger("heat-index-retail", {"temperature": 96, "humidity": 60})
check("heat-index-retail fires on hot + humid", r["active"], True)
r = wt.evaluate_trigger("heat-index-retail", {"temperature": 96})
check("heat-index-retail with no humidity is not measured", r["measured"], False)

r = wt.evaluate_trigger("cold-snap-retail", {"temperature": 20})
check("cold-snap-retail fires at 20F", r["active"], True)
r = wt.evaluate_trigger("deep-freeze-retail", {"temperature": 20})
check("deep-freeze-retail does not fire at 20F -- it escalates past cold-snap-retail", r["active"], False)
r = wt.evaluate_trigger("deep-freeze-retail", {"temperature": 5})
check("deep-freeze-retail fires below 10F", r["active"], True)

r = wt.evaluate_trigger("patio-season-open",
                        {"temperature": 75, "rain_probability": 5},
                        today=date(2026, 5, 1))
check("patio-season-open fires on a mild dry May day", r["active"], True)
r = wt.evaluate_trigger("patio-season-open",
                        {"temperature": 75, "rain_probability": 5},
                        today=date(2026, 9, 1))
check("patio-season-open is outside its months in September", r["active"], False)

r = wt.evaluate_trigger("fall-clearance-day",
                        {"temperature": 55, "rain_probability": 10},
                        today=date(2026, 10, 1))
check("fall-clearance-day fires on a mild dry October day", r["active"], True)

state = {}
r = wt.evaluate_trigger("first-frost-shop", {"forecast_low": 25}, state,
                        today=date(2026, 11, 1))
check("first-frost-shop fires the first time it is cold enough", r["active"], True)
r = wt.evaluate_trigger("first-frost-shop", {"forecast_low": 20}, r["state"],
                        today=date(2026, 11, 10))
check("first-frost-shop does not fire again the same season", r["active"], False)

r = wt.evaluate_trigger("storm-prep", {"official_alerts": ["Tornado Warning"]})
check("storm-prep fires on a real alert", r["active"], True)
r = wt.evaluate_trigger("storm-prep", {"official_alerts": []})
check("storm-prep does not fire with no alert", r["active"], False)

r = wt.evaluate_trigger("snow-gear-day", {"snow_inches": 3})
check("snow-gear-day fires at 3in", r["active"], True)
r = wt.evaluate_trigger("snow-gear-day", {"snow_inches": 1})
check("snow-gear-day does not fire at 1in", r["active"], False)

r = wt.evaluate_trigger("wind-advisory", {"wind_mph": 30})
check("wind-advisory fires at 30mph", r["active"], True)
r = wt.evaluate_trigger("wind-advisory", {"wind_mph": 10})
check("wind-advisory does not fire at 10mph", r["active"], False)

r = wt.evaluate_trigger("first-warm-weekend", {"temperature": 70}, today=date(2026, 1, 15))
check("first-warm-weekend fires on a warm January day", r["active"], True)
r = wt.evaluate_trigger("first-warm-weekend", {"temperature": 70}, today=date(2026, 6, 15))
check("first-warm-weekend is outside its months in June", r["active"], False)

# All three once-per-season triggers -- restaurant's first-freeze, hvac's
# first-hard-freeze and retail's first-frost-shop -- key their season state
# on the rule's own once_per_season value ("cold"), not on the trigger id.
# None may see either of the other two's carried state.
r3 = wt.evaluate_trigger("first-frost-shop", {"forecast_low": 20}, {},
                         today=date(2026, 11, 2))
check("first-frost-shop has its own, separate state and fires", r3["active"], True)


section("Auto Repair / Service — the fourth vertical, same rule vocabulary")
r = wt.evaluate_trigger("battery-cold-test", {"temperature": 15})
check("battery-cold-test fires at 15F", r["active"], True)
r = wt.evaluate_trigger("battery-cold-test", {"temperature": 25})
check("battery-cold-test does not fire at 25F", r["active"], False)

r = wt.evaluate_trigger("cold-snap-auto", {"temperature": 20})
check("cold-snap-auto fires at 20F", r["active"], True)
r = wt.evaluate_trigger("deep-freeze-auto", {"temperature": 20})
check("deep-freeze-auto does not fire at 20F -- it escalates past cold-snap-auto", r["active"], False)
r = wt.evaluate_trigger("deep-freeze-auto", {"temperature": -5})
check("deep-freeze-auto fires below 0F", r["active"], True)

r = wt.evaluate_trigger("wind-chill-auto", {"feels_like": 5})
check("wind-chill-auto fires at feels-like 5F", r["active"], True)
r = wt.evaluate_trigger("wind-chill-auto", {"feels_like": 20})
check("wind-chill-auto does not fire at feels-like 20F", r["active"], False)

r = wt.evaluate_trigger("heat-index-auto", {"temperature": 101, "humidity": 60})
check("heat-index-auto fires on hot + humid", r["active"], True)
r = wt.evaluate_trigger("heat-index-auto", {"temperature": 101})
check("heat-index-auto with no humidity is not measured", r["measured"], False)

r = wt.evaluate_trigger("ac-check-early",
                        {"temperature": 88}, today=date(2026, 5, 1))
check("ac-check-early fires on an 88F day in May", r["active"], True)
r = wt.evaluate_trigger("ac-check-early",
                        {"temperature": 88}, today=date(2026, 9, 1))
check("ac-check-early is outside its months in September", r["active"], False)

r = wt.evaluate_trigger("spring-service-day",
                        {"temperature": 72, "rain_probability": 5},
                        today=date(2026, 4, 1))
check("spring-service-day fires on a mild dry April day", r["active"], True)
r = wt.evaluate_trigger("fall-service-day",
                        {"temperature": 60, "rain_probability": 10},
                        today=date(2026, 10, 1))
check("fall-service-day fires on a mild dry October day", r["active"], True)

r = wt.evaluate_trigger("wiper-blade-season", {"rain_probability": 80})
check("wiper-blade-season fires at 80% chance of rain", r["active"], True)
r = wt.evaluate_trigger("wiper-blade-season", {"rain_probability": 30})
check("wiper-blade-season does not fire at 30% chance of rain", r["active"], False)

r = wt.evaluate_trigger("pothole-season",
                        {"temperature": 45}, today=date(2026, 3, 1))
check("pothole-season fires on a 45F day in March", r["active"], True)

r = wt.evaluate_trigger("snow-tire-day", {"snow_inches": 3})
check("snow-tire-day fires at 3in", r["active"], True)
r = wt.evaluate_trigger("snow-tire-day", {"snow_inches": 0.5})
check("snow-tire-day does not fire at 0.5in", r["active"], False)

r = wt.evaluate_trigger("storm-driving-prep", {"official_alerts": ["Ice Storm Warning"]})
check("storm-driving-prep fires on a real alert", r["active"], True)
r = wt.evaluate_trigger("storm-driving-prep", {"official_alerts": []})
check("storm-driving-prep does not fire with no alert", r["active"], False)

state = {}
r = wt.evaluate_trigger("first-freeze-auto", {"forecast_low": 25}, state,
                        today=date(2026, 11, 1))
check("first-freeze-auto fires the first time it is cold enough", r["active"], True)
r = wt.evaluate_trigger("first-freeze-auto", {"forecast_low": 20}, r["state"],
                        today=date(2026, 11, 10))
check("first-freeze-auto does not fire again the same season", r["active"], False)

# All four once-per-season triggers -- restaurant's first-freeze, hvac's
# first-hard-freeze, retail's first-frost-shop and auto's first-freeze-auto --
# key their season state on the rule's own once_per_season value ("cold"),
# not on the trigger id. None may see any of the other three's carried state.
r4 = wt.evaluate_trigger("first-freeze-auto", {"forecast_low": 20}, {},
                         today=date(2026, 11, 2))
check("first-freeze-auto has its own, separate state and fires", r4["active"], True)


section("Landscaping / Lawn Care — the fifth vertical, same rule vocabulary")
r = wt.evaluate_trigger("spring-green-up-day",
                        {"temperature": 65, "rain_probability": 5},
                        today=date(2026, 4, 1))
check("spring-green-up-day fires on a mild dry April day", r["active"], True)
r = wt.evaluate_trigger("spring-green-up-day",
                        {"temperature": 65, "rain_probability": 5},
                        today=date(2026, 8, 1))
check("spring-green-up-day is outside its months in August", r["active"], False)

r = wt.evaluate_trigger("dry-spell-watering", {"temperature": 87, "rain_probability": 5})
check("dry-spell-watering fires at 87F dry", r["active"], True)
r = wt.evaluate_trigger("drought-stress", {"temperature": 87, "rain_probability": 5})
check("drought-stress does not fire at 87F -- it escalates past dry-spell-watering", r["active"], False)
r = wt.evaluate_trigger("drought-stress", {"temperature": 94, "rain_probability": 5})
check("drought-stress fires at 94F dry", r["active"], True)

r = wt.evaluate_trigger("heavy-rain-growth-spurt", {"rain_probability": 80})
check("heavy-rain-growth-spurt fires at 80% chance of rain", r["active"], True)
r = wt.evaluate_trigger("heavy-rain-growth-spurt", {"rain_probability": 30})
check("heavy-rain-growth-spurt does not fire at 30% chance of rain", r["active"], False)

r = wt.evaluate_trigger("storm-cleanup", {"official_alerts": ["Severe Thunderstorm Warning"]})
check("storm-cleanup fires on a real alert", r["active"], True)
r = wt.evaluate_trigger("storm-cleanup", {"official_alerts": []})
check("storm-cleanup does not fire with no alert", r["active"], False)

r = wt.evaluate_trigger("high-wind-debris", {"wind_mph": 35})
check("high-wind-debris fires at 35 mph", r["active"], True)
r = wt.evaluate_trigger("high-wind-debris", {"wind_mph": 15})
check("high-wind-debris does not fire at 15 mph", r["active"], False)

r = wt.evaluate_trigger("fall-leaf-peak",
                        {"temperature": 50, "rain_probability": 10},
                        today=date(2026, 10, 15))
check("fall-leaf-peak fires on a mild dry October day", r["active"], True)

r = wt.evaluate_trigger("first-snow-landscaping", {"snow_inches": 3})
check("first-snow-landscaping fires at 3in", r["active"], True)
r = wt.evaluate_trigger("first-snow-landscaping", {"snow_inches": 0.5})
check("first-snow-landscaping does not fire at 0.5in", r["active"], False)

state = {}
for day_cloudy in (True, True, False, True, True, True):
    r = wt.evaluate_trigger("mosquito-surge", {"cloud_percent": 90 if day_cloudy else 20}, state)
    state = r["state"]
check("mosquito-surge fires on the third consecutive overcast day after a reset", r["active"], True)
r = wt.evaluate_trigger("mosquito-surge", {})
check("mosquito-surge with no cloud data is not measured", r["measured"], False)

r = wt.evaluate_trigger("spring-fertilize-window",
                        {"temperature": 60, "rain_probability": 5},
                        today=date(2026, 3, 15))
check("spring-fertilize-window fires on a mild dry March day", r["active"], True)
r = wt.evaluate_trigger("fall-fertilize-window",
                        {"temperature": 60, "rain_probability": 5},
                        today=date(2026, 9, 15))
check("fall-fertilize-window fires on a mild dry September day", r["active"], True)

r = wt.evaluate_trigger("heat-wave-lawn-stress", {"temperature": 101, "humidity": 60})
check("heat-wave-lawn-stress fires on hot + humid", r["active"], True)
r = wt.evaluate_trigger("heat-wave-lawn-stress", {"temperature": 101})
check("heat-wave-lawn-stress with no humidity is not measured", r["measured"], False)

state = {}
r = wt.evaluate_trigger("first-freeze-landscaping", {"forecast_low": 25}, state,
                        today=date(2026, 11, 1))
check("first-freeze-landscaping fires the first time it is cold enough", r["active"], True)
r = wt.evaluate_trigger("first-freeze-landscaping", {"forecast_low": 20}, r["state"],
                        today=date(2026, 11, 10))
check("first-freeze-landscaping does not fire again the same season", r["active"], False)

r5 = wt.evaluate_trigger("first-freeze-landscaping", {"forecast_low": 20}, {},
                         today=date(2026, 11, 2))
check("first-freeze-landscaping has its own, separate state and fires", r5["active"], True)


section("Pool & Spa Service — the sixth vertical, same rule vocabulary")
r = wt.evaluate_trigger("pool-opening-day",
                        {"temperature": 70, "rain_probability": 5},
                        today=date(2026, 4, 1))
check("pool-opening-day fires on a mild dry April day", r["active"], True)
r = wt.evaluate_trigger("pool-opening-day",
                        {"temperature": 70, "rain_probability": 5},
                        today=date(2026, 8, 1))
check("pool-opening-day is outside its months in August", r["active"], False)

r = wt.evaluate_trigger("chlorine-burn-off", {"temperature": 86, "rain_probability": 5})
check("chlorine-burn-off fires at 86F dry", r["active"], True)
r = wt.evaluate_trigger("algae-bloom-risk", {"temperature": 90, "humidity": 65})
check("algae-bloom-risk fires on heat stacked with humidity", r["active"], True)
r = wt.evaluate_trigger("algae-bloom-risk", {"temperature": 90})
check("algae-bloom-risk with no humidity is not measured", r["measured"], False)

r = wt.evaluate_trigger("heavy-rain-dilution", {"rain_probability": 80})
check("heavy-rain-dilution fires at 80% chance of rain", r["active"], True)
r = wt.evaluate_trigger("heavy-rain-dilution", {"rain_probability": 30})
check("heavy-rain-dilution does not fire at 30% chance of rain", r["active"], False)

r = wt.evaluate_trigger("storm-debris-cleanup", {"official_alerts": ["Severe Thunderstorm Warning"]})
check("storm-debris-cleanup fires on a real alert", r["active"], True)
r = wt.evaluate_trigger("storm-debris-cleanup", {"official_alerts": []})
check("storm-debris-cleanup does not fire with no alert", r["active"], False)

r = wt.evaluate_trigger("high-wind-debris-pool", {"wind_mph": 30})
check("high-wind-debris-pool fires at 30 mph", r["active"], True)
r = wt.evaluate_trigger("high-wind-debris-pool", {"wind_mph": 10})
check("high-wind-debris-pool does not fire at 10 mph", r["active"], False)

r = wt.evaluate_trigger("hard-freeze-pool", {"temperature": 15})
check("hard-freeze-pool fires at 15F", r["active"], True)
r = wt.evaluate_trigger("deep-freeze-pool", {"temperature": 15})
check("deep-freeze-pool does not fire at 15F -- it escalates past hard-freeze-pool", r["active"], False)
r = wt.evaluate_trigger("deep-freeze-pool", {"temperature": -5})
check("deep-freeze-pool fires below 0F", r["active"], True)

r = wt.evaluate_trigger("pool-closing-day",
                        {"temperature": 60, "rain_probability": 10},
                        today=date(2026, 9, 15))
check("pool-closing-day fires on a mild dry September day", r["active"], True)

r = wt.evaluate_trigger("spa-season-open", {"temperature": 38})
check("spa-season-open fires at 38F", r["active"], True)
r = wt.evaluate_trigger("spa-season-open", {"temperature": 60})
check("spa-season-open does not fire at 60F", r["active"], False)

r = wt.evaluate_trigger("evaporation-watch", {"temperature": 93, "rain_probability": 5})
check("evaporation-watch fires at 93F dry", r["active"], True)

r = wt.evaluate_trigger("heat-wave-pool-surge", {"temperature": 96})
check("heat-wave-pool-surge fires at 96F", r["active"], True)
r = wt.evaluate_trigger("heat-wave-pool-surge", {"temperature": 80})
check("heat-wave-pool-surge does not fire at 80F", r["active"], False)

state = {}
r = wt.evaluate_trigger("first-freeze-pool", {"forecast_low": 25}, state,
                        today=date(2026, 11, 1))
check("first-freeze-pool fires the first time it is cold enough", r["active"], True)
r = wt.evaluate_trigger("first-freeze-pool", {"forecast_low": 20}, r["state"],
                        today=date(2026, 11, 10))
check("first-freeze-pool does not fire again the same season", r["active"], False)

# All six once-per-season "cold" triggers across every vertical carry
# separate state, keyed on the campaign's own trigger_id in store.py, not on
# the shared rule value they all use ("cold").
r6 = wt.evaluate_trigger("first-freeze-pool", {"forecast_low": 20}, {},
                         today=date(2026, 11, 2))
check("first-freeze-pool has its own, separate state and fires", r6["active"], True)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
