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
check("fourteen triggers", len(wt.TRIGGERS), 14)
check("all restaurant for v1", all(t.vertical == "restaurant" for t in wt.TRIGGERS.values()), True)
check("cap is three", wt.MAX_TRIGGERS, 3)
check("every month has every trigger", all(
    set(wt.month_order("restaurant", m)) == set(wt.triggers_for_vertical("restaurant"))
    for m in wt.MONTH_ABBR), True)
check("spec's Sep example order is honored (first four)",
     wt.month_order("restaurant", "Sep")[:4],
     ["patio-day", "first-cool-night", "rain-delay", "crisp-day"])
check("unknown month falls back to the registry order",
     set(wt.month_order("restaurant", "Nope")), set(wt.TRIGGERS))


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


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
