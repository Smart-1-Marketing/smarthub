"""Cam Builder -- address in, sources out.

Step 1 geocodes the address through the Census geocoder (free, unlimited,
authoritative for US addresses, no terms restriction on storing the result).
Several matches are returned rather than picked: a wrong pin silently gives
the wrong NWS zone, which at Buckeye Lake is a real failure and not a
theoretical one. Step 2 runs every adapter's probe concurrently and answers
found / needs a decision / not available here, with the reason -- because
"why is water temperature absent" is worth more in a client conversation
than the tile would have been.

Steps 3 and 4 (the review screen and provisioning) are Sprint 6; the library
half is here so the seed and the staff probe endpoint use the same code the
screen will.
"""
from __future__ import annotations

from . import adapters
from .adapters.http import SourceError, get_json

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
LOCATION_TYPES = ("inland_lake", "river", "coastal", "great_lakes", "ski", "golf", "town")

# What each location type would like to show, so the review screen can say
# which tiles are switched off and why.
WANTS = {
    "inland_lake": ("weather_now", "forecast_daily", "alerts", "observation", "lake_level",
                    "advisories", "astronomy", "pool_elevation", "water", "air_quality"),
    "river": ("weather_now", "forecast_daily", "alerts", "observation", "streamflow",
              "gauge_height", "water_temp", "astronomy"),
    "coastal": ("weather_now", "forecast_daily", "alerts", "observation", "water", "tides", "astronomy"),
    "great_lakes": ("weather_now", "forecast_daily", "alerts", "observation", "water", "astronomy"),
    "ski": ("weather_now", "forecast_daily", "alerts", "observation", "astronomy"),
    "golf": ("weather_now", "forecast_daily", "alerts", "observation", "astronomy"),
    "town": ("weather_now", "forecast_daily", "alerts", "observation", "astronomy"),
}


def geocode(address: str) -> list[dict]:
    data = get_json(CENSUS, params={"address": address, "benchmark": "Public_AR_Current",
                                    "format": "json"})
    out = []
    for m in (data.get("result") or {}).get("addressMatches") or []:
        c = m.get("coordinates") or {}
        comp = m.get("addressComponents") or {}
        try:
            out.append({"address": m.get("matchedAddress"), "lat": round(float(c["y"]), 6),
                        "lon": round(float(c["x"]), 6), "city": comp.get("city"),
                        "state": comp.get("state"), "zip": comp.get("zip")})
        except (KeyError, TypeError, ValueError):
            continue
    if not out:
        raise SourceError("the Census geocoder found no match for that address")
    return out


def probe(lat: float, lon: float, location_type: str = "inland_lake") -> dict:
    """The review-screen groups: confirmed, decide, absent."""
    if location_type not in LOCATION_TYPES:
        raise ValueError(f"location type must be one of {', '.join(LOCATION_TYPES)}")
    rows = adapters.probe_all(lat, lon, location_type)
    found = [r for r in rows if r.get("found")]
    absent = [r for r in rows if not r.get("found")]
    by_key: dict[str, list] = {}
    for r in found:
        by_key.setdefault(r["key"], []).append(r)
    confirmed, decide = [], []
    for key, cands in by_key.items():
        cands.sort(key=lambda r: r.get("distance_mi") or 0)
        (decide if len(cands) > 1 else confirmed).append({"key": key, "candidates": cands})
    wanted = WANTS.get(location_type, WANTS["inland_lake"])
    missing = [{"key": k, "reason": next((r["detail"] for r in absent if r["key"] == k),
                                          "no adapter proposes this here")}
               for k in wanted if k not in by_key]
    return {"lat": lat, "lon": lon, "location_type": location_type,
            "confirmed": confirmed, "decide": decide, "absent": missing, "raw": rows}
