"""The conditions strip is a registry, not a fixed layout.

**Every tile is optional. A tile with no data does not render.** Not a dash,
not "N/A", not a greyed-out placeholder -- it is absent, and the remaining
tiles redistribute to fill the row. Fewer than three survivors and the strip
itself does not render; two lonely tiles look broken, no strip looks
intentional.

Each tile names the cache keys it reads, a staleness tolerance of its own,
and a formatter. Past tolerance the tile collapses rather than lying: that
is the mechanism that replaces a manual-entry override, because nothing
waits on a human and nothing wrong stays up.

Eight tiles are defined. At Buckeye Lake five render; water temperature and
chop have no source there and air quality is deferred. At a coastal client
the same registry lights those up from NOAA buoys, which is what makes one
codebase serve every location.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MIN_TILES = 3


def fresh(cache: dict, key: str, tolerance_minutes: int, now: datetime) -> dict | None:
    """The cached payload for `key` when it was fetched inside the tolerance."""
    row = cache.get(key) or {}
    payload, fetched = row.get("payload"), row.get("fetched_at")
    if not payload or not fetched:
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    if now - fetched > timedelta(minutes=tolerance_minutes):
        return None
    return payload


def _local(iso_text: str, tz_name: str) -> datetime | None:
    if not iso_text:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz_name or "UTC"))


def clock(iso_text: str, tz_name: str) -> str:
    dt = _local(iso_text, tz_name)
    if not dt:
        return ""
    return dt.strftime("%I:%M").lstrip("0")


def _weather(cache, now, tolerance):
    """Grid first, the nearest station as the fallback path -- a different
    endpoint with a different failure mode."""
    grid = fresh(cache, "weather_now", tolerance, now)
    if grid:
        return grid, "NWS grid"
    obs = fresh(cache, "observation", tolerance, now)
    if obs:
        label = obs.get("station_name") or obs.get("station") or "NWS station"
        if obs.get("distance_mi"):
            label = f"{label}, {obs['distance_mi']:g} mi"
        return obs, label
    return None, ""


def tile_air(cache, page, now, tolerance):
    wx, src = _weather(cache, now, tolerance)
    if not wx or wx.get("temp_f") is None:
        return None
    sub = f"Feels {wx['feels_f']}°" if wx.get("feels_f") is not None else (wx.get("text") or "")
    return {"label": "Air", "value": str(wx["temp_f"]), "unit": "°F", "sub": sub, "source": src}


def tile_wind(cache, page, now, tolerance):
    wx, src = _weather(cache, now, tolerance)
    if not wx or wx.get("wind_mph") is None:
        return None
    value = f"{wx.get('wind_dir') or ''} {wx['wind_mph']}".strip()
    sub = f"Gusts to {wx['gust_mph']} mph" if wx.get("gust_mph") else "mph"
    # An inland grid or an airport 12 miles off is regional, not on-lake, and
    # the tile says so rather than implying a dock anemometer.
    if page.get("location_type") in ("inland_lake", "river") or (wx.get("distance_mi") or 0) > 3:
        sub += " · regional"
    return {"label": "Wind", "value": value, "unit": "", "sub": sub, "source": src}


def tile_lake_level(cache, page, now, tolerance):
    lake = fresh(cache, "lake_level", tolerance, now)
    if not lake or lake.get("elevation_ft") is None:
        return None
    pool = fresh(cache, "pool_elevation", 60 * 24 * 600, now) or {}
    normal = pool.get("normal_ft")
    elev = float(lake["elevation_ft"])
    src = f"USGS {lake.get('site') or 'gauge'}"
    if normal is not None and (page.get("config") or {}).get("pool_datum_confirmed"):
        delta = round(elev - float(normal), 1)
        word = "above" if delta > 0 else "below" if delta < 0 else "at"
        value = f"{delta:+.1f}" if delta else "0.0"
        sub = f"{elev:.2f} ft · {word} normal pool" if delta else f"{elev:.2f} ft · at normal pool"
        return {"label": "Lake level", "value": value, "unit": "ft", "sub": sub, "source": src}
    sub = f"normal pool {float(normal):.1f} ft" if normal is not None else "surface elevation"
    if normal is not None:
        sub += " · datum unconfirmed"
    return {"label": "Lake level", "value": f"{elev:.2f}", "unit": "ft", "sub": sub, "source": src}


def tile_water_temp(cache, page, now, tolerance):
    for key in ("water", "tides", "water_temp"):
        p = fresh(cache, key, tolerance, now)
        if p and p.get("water_temp_f") is not None:
            src = p.get("station_name") or p.get("site_name") or p.get("source") or key
            return {"label": "Water", "value": str(p["water_temp_f"]), "unit": "°F",
                    "sub": "water temperature", "source": src}
    return None


def tile_chop(cache, page, now, tolerance):
    p = fresh(cache, "water", tolerance, now)
    if not p or p.get("wave_height_ft") is None:
        return None
    sub = f"period {p['wave_period_s']:g} s" if p.get("wave_period_s") else "wave height"
    return {"label": "Waves", "value": f"{p['wave_height_ft']:g}", "unit": "ft", "sub": sub,
            "source": p.get("station_name") or "NOAA buoy"}


def tile_sky(cache, page, now, tolerance):
    wx = fresh(cache, "weather_now", tolerance, now)
    if not wx:
        return None
    thunder = wx.get("day_max_thunder_pct")
    precip = wx.get("day_max_precip_pct")
    if thunder is not None and thunder >= 20:
        return {"label": "Sky", "value": str(thunder), "unit": "%", "sub": "Storm chance today", "source": "NWS grid"}
    if precip is not None and precip >= 20:
        return {"label": "Sky", "value": str(precip), "unit": "%", "sub": "Rain chance today", "source": "NWS grid"}
    if wx.get("sky_pct") is None:
        return None
    return {"label": "Sky", "value": str(wx["sky_pct"]), "unit": "%", "sub": "Cloud cover", "source": "NWS grid"}


def tile_sunset(cache, page, now, tolerance):
    a = fresh(cache, "astronomy", tolerance, now)
    if not a or not a.get("sunset"):
        return None
    local = _local(a["sunset"], page.get("timezone"))
    value = local.strftime("%I:%M").lstrip("0") if local else ""
    sub = "PM"
    note = (page.get("config") or {}).get("night_speed_note")
    if note:
        sub = f"PM · {note}"
    return {"label": "Sunset", "value": value, "unit": "", "sub": sub, "source": "Computed"}


def tile_air_quality(cache, page, now, tolerance):
    p = fresh(cache, "air_quality", tolerance, now)
    if not p or p.get("aqi") is None:
        return None
    return {"label": "Air quality", "value": str(p["aqi"]), "unit": "AQI",
            "sub": p.get("category") or "", "source": "AirNow"}


# key, formatter, tolerance in minutes, sort weight
TILES = (
    ("air", tile_air, 90, 10),
    ("wind", tile_wind, 90, 20),
    ("lake_level", tile_lake_level, 120, 30),
    ("water_temp", tile_water_temp, 90, 40),
    ("chop", tile_chop, 90, 50),
    ("sky", tile_sky, 90, 60),
    ("sunset", tile_sunset, 60 * 48, 70),
    ("air_quality", tile_air_quality, 180, 80),
)


def build_strip(page: dict, cache: dict, now: datetime | None = None) -> dict:
    """{"tiles": [...], "rendered": bool, "dropped": [{"key", "reason"}]}."""
    now = now or datetime.now(timezone.utc)
    tiles, dropped = [], []
    for key, fn, tolerance, weight in TILES:
        try:
            tile = fn(cache, page, now, tolerance)
        except Exception as exc:  # noqa: BLE001 -- a formatter bug drops its tile, not the page
            tile, reason = None, f"{type(exc).__name__}: {exc}"
        else:
            reason = "no fresh source"
        if tile:
            tiles.append({"key": key, "weight": weight, **tile})
        else:
            dropped.append({"key": key, "reason": reason})
    tiles.sort(key=lambda t: t["weight"])
    return {"tiles": tiles, "rendered": len(tiles) >= MIN_TILES, "dropped": dropped}
