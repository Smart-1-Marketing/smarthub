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


def slugify(text: str, fallback: str = "cam") -> str:
    """A URL slug that always resolves to something. `fallback` covers a
    string that would collapse to empty (all punctuation, non-ASCII)."""
    import re
    out = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (out or fallback)[:80]


def spec_from_answers(answers: dict, probe_result: dict) -> dict:
    """Assemble a CamHub page spec from the wizard's answers and the
    accepted probe rows. This is what `seeds.provision()` would take -- one
    dict, `sources` on it, drop it into `seeds.SEEDS` and it is a seed.

    The wizard's own review step decides which candidate to keep for a
    key with more than one; here that decision is honoured verbatim.
    Every source arrives already picked -- no source is enabled
    automatically because it was found in the probe.
    """
    slug = slugify(answers.get("slug") or answers.get("title") or "", "cam")
    title = (answers.get("title") or "").strip()[:200] or f"{answers.get('location_name') or 'Live'} Cam"
    lat = float(probe_result.get("lat") or 0.0)
    lon = float(probe_result.get("lon") or 0.0)
    picked = answers.get("picked") or {}
    sources = []
    for group in probe_result.get("confirmed", []):
        cand = (group.get("candidates") or [None])[0]
        if cand and picked.get(group["key"], True):
            sources.append(_source_from_candidate(cand))
    for group in probe_result.get("decide", []):
        chosen_idx = picked.get(group["key"])
        if isinstance(chosen_idx, int) and 0 <= chosen_idx < len(group["candidates"]):
            sources.append(_source_from_candidate(group["candidates"][chosen_idx]))
    return {
        "slug": slug,
        "title": title,
        "client_name": (answers.get("client_name") or "").strip()[:120],
        "business_name": (answers.get("business_name") or "").strip()[:120],
        "location_name": (answers.get("location_name") or "").strip()[:120],
        "address": (answers.get("address") or "").strip()[:240],
        "lat": lat, "lon": lon,
        "timezone": (answers.get("timezone") or "America/New_York")[:60],
        "location_type": probe_result.get("location_type") or "inland_lake",
        "cam_embed_url": "",
        "cam_embed_type": "youtube",
        "cam_caption": (answers.get("cam_caption") or "").strip()[:240],
        "seo_html": "",
        "status": "live",
        "config": _config_scaffold(answers, title),
        "sources": sources,
    }


def _source_from_candidate(cand: dict) -> dict:
    """One probe row → one persistable source row. The adapter and the
    key are the candidate's, the cadence is a sensible default per-kind,
    and the config is what the adapter's own probe filed."""
    key = cand["key"]
    cadence = {
        "weather_now": 10, "forecast_daily": 60, "alerts": 15,
        "observation": 10, "lake_level": 15, "streamflow": 15,
        "gauge_height": 15, "water_temp": 30, "tides": 30, "water": 30,
        "waves": 30, "advisories": 60, "astronomy": 360,
        "pool_elevation": 60 * 24 * 365,
    }.get(key, 60)
    tolerance = {
        "weather_now": 90, "forecast_daily": 180, "alerts": 90,
        "observation": 90, "lake_level": 120, "streamflow": 120,
        "gauge_height": 120, "water_temp": 240, "tides": 120, "water": 240,
        "waves": 240, "advisories": 1440, "astronomy": 2880,
        "pool_elevation": 60 * 24 * 600,
    }.get(key, 90)
    return {"key": key, "adapter": cand["adapter"],
            "label": cand.get("label") or key,
            "config": cand.get("config") or {},
            "cadence_minutes": cadence, "tolerance_minutes": tolerance}


def _config_scaffold(answers: dict, title: str) -> dict:
    """The page-config skeleton the render layer expects. Every field the
    template reads has a defined default so a page shipped straight from
    the wizard renders without landmines."""
    biz = {"name": (answers.get("business_name") or "").strip()[:120],
           "street": (answers.get("street") or "").strip()[:120],
           "city": (answers.get("city") or "").strip()[:80],
           "state": (answers.get("state") or "").strip()[:2],
           "zip": (answers.get("zip") or "").strip()[:12],
           "url": (answers.get("website_url") or "").strip()[:300],
           "phone": (answers.get("phone") or "").strip()[:40]}
    location_name = (answers.get("location_name") or "").strip()[:120]
    return {
        "h1": title,
        "place": ", ".join([biz["city"], biz["state"]]).strip(", "),
        "path": (answers.get("path") or "/live-cam")[:120],
        "canonical_url": (answers.get("canonical_url") or "").strip()[:300],
        "cam_label": (answers.get("cam_label") or "Live cam")[:80],
        "night_speed_note": "",
        "pool_datum_confirmed": False,
        "business": biz,
        "lake": {"name": location_name,
                 "description": (answers.get("location_blurb") or "").strip()[:800]},
        "links": {"reservations": "", "tastings": "", "events": ""},
        "convert": {"heading": (answers.get("convert_heading") or "").strip()[:120],
                    "body": (answers.get("convert_body") or "").strip()[:400]},
        "house_ads": {
            "presenting": {"name": biz["name"] or location_name,
                           "badge": ((biz["name"] or location_name or "?")[:1] or "?").upper(),
                           "sub": f"Present the {location_name or 'live cam'}",
                           "eyebrow": "Sponsorship available",
                           "headline": "This slot is available to one local business.",
                           "body": "Exclusive presenting sponsorship of the cam -- your logo above the "
                                   "stream on every view, a feature block below, and a monthly performance report.",
                           "cta": "Sponsor the cam", "url": ""},
            "supporting": [
                {"name": "Sponsor this tile", "body": "Reach visitors while they plan the day.",
                 "cta": "Get the rate card", "url": ""},
                {"name": "Sponsor this tile", "body": "Reach visitors while they plan the day.",
                 "cta": "Get the rate card", "url": ""},
                {"name": "Sponsor this tile", "body": "Reach visitors while they plan the day.",
                 "cta": "Get the rate card", "url": ""},
                {"name": "Sponsor this tile", "body": "Reach visitors while they plan the day.",
                 "cta": "Get the rate card", "url": ""},
            ],
        },
        "theme": {"wine_deep": "#0f172a", "wine": "#1e293b", "wine_lift": "#334155",
                  "gold": "#f59e0b", "gold_bright": "#fbbf24", "ink": "#f8fafc"},
    }
