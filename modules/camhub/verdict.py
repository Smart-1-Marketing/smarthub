"""'Good day on the water?' -- one derived line, green, amber or red, with a
plain-English reason. Pure logic over data already fetched: no extra API, no
extra cost, and the most shareable thing on the page.

The thresholds are boating thresholds, not weather-app ones. Wind is the
number that decides whether someone launches, so it leads; an active NWS
warning or a severe beach advisory overrides everything; and after sunset
the note is the lake's own night rule rather than a forecast.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .tiles import _local, fresh

LEVELS = {"good": "Good", "caution": "Caution", "poor": "Poor"}
WARNING_EVENTS = ("Warning",)
# A Watch is issued at "Severe" too, so severity alone would turn a Flood
# Watch into "not a day for the open water"; the event name decides, and
# only Extreme overrides it.
SEVERE = ("Extreme",)
DIRECTIONS = {"N": "north", "NE": "northeast", "E": "east", "SE": "southeast",
              "S": "south", "SW": "southwest", "W": "west", "NW": "northwest"}


def _chop(wind: int | None) -> str:
    if wind is None:
        return ""
    if wind < 10:
        return "light chop"
    if wind < 16:
        return "moderate chop"
    return "rough water"


def _storm_note(wx: dict, tz_name: str) -> str:
    """'Storms possible after 4 PM' from the hours ahead, or ''."""
    for hour in wx.get("hours_ahead") or []:
        pct = hour.get("thunder_pct")
        if pct is not None and pct >= 30:
            local = _local(hour.get("at"), tz_name)
            if local:
                return f"Storms possible after {local.strftime('%I %p').lstrip('0')}"
            return "Storms possible later today"
    return ""


def verdict(page: dict, cache: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    tz_name = page.get("timezone") or "America/New_York"
    wx = fresh(cache, "weather_now", 90, now) or fresh(cache, "observation", 90, now) or {}
    alerts = (fresh(cache, "alerts", 90, now) or {}).get("alerts") or []
    beach = fresh(cache, "advisories", 60 * 24, now) or {}
    lake = fresh(cache, "lake_level", 120, now) or {}
    pool = fresh(cache, "pool_elevation", 60 * 24 * 600, now) or {}
    astro = fresh(cache, "astronomy", 60 * 48, now) or {}
    cfg = page.get("config") or {}

    wind, gust = wx.get("wind_mph"), wx.get("gust_mph")
    reasons: list[str] = []
    level = "good"

    warnings = [a for a in alerts if (a.get("severity") in SEVERE
                                       or any(w in str(a.get("event") or "") for w in WARNING_EVENTS))]
    watches = [a for a in alerts if a not in warnings]
    if warnings:
        level = "poor"
        reasons.append(f"{warnings[0].get('event') or 'A weather warning'} is in effect")
    elif wind is not None and (wind >= 20 or (gust or 0) >= 28):
        level = "poor"
    elif wind is not None and (wind >= 12 or (gust or 0) >= 18):
        level = "caution"
    if watches and level == "good":
        level = "caution"
        reasons.append(f"{watches[0].get('event') or 'A weather watch'} is in effect")

    if wind is not None:
        direction = DIRECTIONS.get(str(wx.get("wind_dir") or "").upper(), "")
        direction = f"{direction} " if direction else ""
        descriptor = "light" if wind < 10 else "moderate" if wind < 16 else "strong"
        text = f"{descriptor} {direction}wind at {wind} mph"
        if gust and gust > wind + 4:
            text += f", gusting to {gust}"
        chop = _chop(wind)
        if chop:
            text += f", {chop}"
        reasons.append(text)

    normal = pool.get("normal_ft")
    if lake.get("elevation_ft") is not None and normal is not None and cfg.get("pool_datum_confirmed"):
        delta = float(lake["elevation_ft"]) - float(normal)
        if abs(delta) >= 0.25:
            reasons.append(f"lake {abs(delta):.1f} ft {'above' if delta > 0 else 'below'} normal pool")

    storm = _storm_note(wx, tz_name)
    thunder = wx.get("day_max_thunder_pct") or 0
    if storm:
        reasons.append(storm.lower() + " — watch the sky if you're heading out late")
        if level == "good" and thunder >= 40:
            level = "caution"

    swim_note = ""
    active_beach = [a for a in beach.get("active") or [] if beach.get("in_season", True)]
    if active_beach:
        worst = max(active_beach, key=lambda a: a.get("severity") or 0)
        swim_note = (f"{worst.get('type') or 'A water advisory'} is active at "
                     f"{beach.get('beach_name') or 'the public beach'}; keep people and pets out of the water")
        if level == "good":
            level = "caution"

    after_dark = False
    sunset = _local(astro.get("sunset"), tz_name) if astro.get("sunset") else None
    if sunset and now.astimezone(ZoneInfo(tz_name)) > sunset and cfg.get("night_speed_note"):
        after_dark = True
        reasons.append(cfg["night_speed_note"] + " on the whole lake")

    if level == "poor":
        headline = "Not a day for the open water."
    elif level == "caution":
        headline = ("Fine for boating, not for swimming." if swim_note and wind is not None and wind < 12
                    else "Usable, with care.")
    else:
        headline = "Good day on the water."
    if wind is None and not alerts and not swim_note:
        headline = "Conditions are being read."
        reasons = ["The weather feed has not answered yet; the tiles above fill in as it does."]
    body = ". ".join(r[0].upper() + r[1:] for r in reasons if r)
    if swim_note:
        body = (body + ". " if body else "") + swim_note
    if body and not body.endswith("."):
        body += "."
    return {"level": level, "key": LEVELS[level], "headline": headline, "body": body,
            "after_dark": after_dark, "wind_mph": wind, "gust_mph": gust}
