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
check("two verticals", set(wt.VERTICALS), {"restaurant", "hvac"})
check("fourteen restaurant triggers", len(wt.triggers_for_vertical("restaurant")), 14)
check("thirteen hvac triggers", len(wt.triggers_for_vertical("hvac")), 13)
check("every trigger is one of the two verticals",
     all(t.vertical in wt.VERTICALS for t in wt.TRIGGERS.values()), True)
check("no trigger id is used by two verticals",
     len(wt.TRIGGERS), len(wt.triggers_for_vertical("restaurant")) + len(wt.triggers_for_vertical("hvac")))
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
check("unknown month falls back to the registry order, restaurant",
     set(wt.month_order("restaurant", "Nope")), set(wt.triggers_for_vertical("restaurant")))
check("unknown month falls back to the registry order, hvac",
     set(wt.month_order("hvac", "Nope")), set(wt.triggers_for_vertical("hvac")))
check("a vertical's month order never leaks the other vertical's ids",
     bool(set(wt.month_order("hvac", "Jan")) & set(wt.triggers_for_vertical("restaurant"))), False)


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


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
