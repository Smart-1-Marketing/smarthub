"""National Weather Service -- api.weather.gov. Free, no key, public domain.

Four kinds of source live on this one adapter, picked by `config["kind"]`:

    gridpoint   the 59-field grid series: current temperature, wind, gust,
                sky, thunder probability, and the hours ahead the verdict
                reads ("storms possible after 4 PM")
    forecast    the seven-day, with wind on every day -- this is a boating
                page
    alerts      watches and warnings, queried by coordinate so NWS resolves
                the zone. Buckeye Lake straddles three zones across two
                offices; the wrong zone silently gives the wrong alerts, and
                on the day it was tested the winery's point had none while
                the mid-lake zone had a Flood Watch. `zones` adds the
                neighbors for lake-wide events.
    station     the nearest real observation. At Buckeye Lake that is
                Newark-Heath Airport, 12 miles north -- labeled as such
                wherever it appears, never implied to be on the lake.

The gridpoint returns Celsius and km/h; the page is Fahrenheit and mph.
"""
from __future__ import annotations

from datetime import timedelta

from .http import SourceError, get_json
from .units import (c_to_f, compass, haversine_mi, kmh_to_mph, m_to_mi,
                    parse_iso, utc_now)

BASE = "https://api.weather.gov"
KINDS = ("gridpoint", "forecast", "alerts", "station")


# ----------------------------------------------------------------- discovery

def points(lat: float, lon: float) -> dict:
    """`/points/{lat},{lon}` -- office, grid, zones, timezone, station list."""
    data = get_json(f"{BASE}/points/{lat:.4f},{lon:.4f}")
    p = data.get("properties") or {}
    zone = str(p.get("forecastZone") or "").rsplit("/", 1)[-1]
    county = str(p.get("county") or "").rsplit("/", 1)[-1]
    return {
        "office": p.get("gridId"), "grid_x": p.get("gridX"), "grid_y": p.get("gridY"),
        "forecast_zone": zone, "county_zone": county,
        "timezone": p.get("timeZone"), "radar": p.get("radarStation"),
        "stations_url": p.get("observationStations"),
        "forecast_url": p.get("forecast"), "hourly_url": p.get("forecastHourly"),
    }


def nearest_station(stations_url: str, lat: float, lon: float) -> dict | None:
    data = get_json(stations_url)
    best = None
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        dist = haversine_mi(lat, lon, coords[1], coords[0])
        row = {"id": props.get("stationIdentifier"), "name": props.get("name"),
               "lat": coords[1], "lon": coords[0], "distance_mi": dist}
        if row["id"] and (best is None or dist < best["distance_mi"]):
            best = row
    return best


def marine_zone_count(state: str) -> int:
    """How many marine zones a state has. Zero for Ohio -- marine products
    exist only for the Great Lakes and the coasts, so a marine-warning tile
    inland would sit dead forever."""
    if not state:
        return 0
    data = get_json(f"{BASE}/zones", params={"type": "marine", "area": state})
    return len(data.get("features") or [])


def probe(lat: float, lon: float, location_type: str = "inland_lake") -> list[dict]:
    p = points(lat, lon)
    if not p.get("office"):
        return [{"key": "weather_now", "adapter": "nws", "label": "NWS forecast grid",
                 "found": False, "config": {}, "detail": "no forecast office for this point"}]
    grid = {"kind": "gridpoint", "office": p["office"], "grid_x": p["grid_x"],
            "grid_y": p["grid_y"], "timezone": p.get("timezone")}
    rows = [
        {"key": "weather_now", "adapter": "nws", "label": "NWS forecast grid (current conditions)",
         "found": True, "config": grid, "cadence_minutes": 10,
         "detail": f"Office {p['office']}, grid {p['grid_x']},{p['grid_y']}, zone {p['forecast_zone']}, radar {p.get('radar')}"},
        {"key": "forecast_daily", "adapter": "nws", "label": "NWS seven-day forecast",
         "found": True, "config": {**grid, "kind": "forecast"}, "cadence_minutes": 60,
         "detail": f"Office {p['office']}, grid {p['grid_x']},{p['grid_y']}"},
        {"key": "alerts", "adapter": "nws", "label": "NWS watches and warnings",
         "found": True, "cadence_minutes": 15,
         "config": {"kind": "alerts", "lat": lat, "lon": lon, "zones": [],
                    "own_zone": p["forecast_zone"]},
         "detail": f"By coordinate; zone {p['forecast_zone']} ({p['county_zone']})"},
    ]
    # Whether marine products exist here at all. Zero for Ohio, 21 for Lake
    # Erie: a marine-warning tile on an inland lake would sit dead forever,
    # so the row says so rather than proposing one.
    state = (p.get("forecast_zone") or "")[:2]
    try:
        marine = marine_zone_count(state)
        rows.append({"key": "marine_alerts", "adapter": "nws", "label": "NWS marine zone warnings",
                     "found": marine > 0, "cadence_minutes": 15,
                     "config": ({"kind": "alerts", "lat": lat, "lon": lon, "zones": [],
                                 "marine": True} if marine else {}),
                     "detail": (f"{marine} marine zone{'s' if marine != 1 else ''} in {state}"
                                if marine else f"no marine zones in {state or 'this state'}; "
                                "marine products exist only for the Great Lakes and the coasts")})
    except SourceError as exc:
        rows.append({"key": "marine_alerts", "adapter": "nws", "label": "NWS marine zone warnings",
                     "found": False, "config": {}, "detail": f"zone list failed: {exc}"})
    station = None
    if p.get("stations_url"):
        try:
            station = nearest_station(p["stations_url"], lat, lon)
        except SourceError as exc:
            rows.append({"key": "observation", "adapter": "nws", "label": "Nearest NWS observation",
                         "found": False, "config": {}, "detail": f"station list failed: {exc}"})
    if station:
        rows.append({"key": "observation", "adapter": "nws", "label": "Nearest NWS observation",
                     "found": True, "cadence_minutes": 10, "distance_mi": station["distance_mi"],
                     "config": {"kind": "station", "station": station["id"],
                                "station_name": station["name"],
                                "distance_mi": station["distance_mi"]},
                     "detail": f"{station['name']} ({station['id']}), {station['distance_mi']} mi"})
    return rows


# --------------------------------------------------------------------- fetch

def fetch(config: dict) -> dict:
    kind = config.get("kind")
    if kind == "gridpoint":
        return fetch_gridpoint(config)
    if kind == "forecast":
        return fetch_forecast(config)
    if kind == "alerts":
        return fetch_alerts(config)
    if kind == "station":
        return fetch_station(config)
    raise SourceError(f"nws: unknown kind '{kind}' (one of {', '.join(KINDS)})")


def _grid_url(config: dict) -> str:
    try:
        return f"{BASE}/gridpoints/{config['office']}/{int(config['grid_x'])},{int(config['grid_y'])}"
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError("nws gridpoint: office and grid coordinates are required") from exc


def _series(props: dict, name: str) -> list[dict]:
    """[{start, hours, value}] for one grid property, in order."""
    out = []
    for item in (props.get(name) or {}).get("values") or []:
        vt = str(item.get("validTime") or "")
        start_text, _, dur = vt.partition("/")
        start = parse_iso(start_text)
        if start is None:
            continue
        hours = _duration_hours(dur)
        out.append({"start": start, "hours": hours, "value": item.get("value")})
    return out


def _duration_hours(dur: str) -> float:
    """ISO-8601 duration to hours. NWS writes PT1H, PT6H, P1D, P1DT6H."""
    dur = (dur or "PT1H").upper().lstrip("P")
    days, _, rest = dur.partition("D") if "D" in dur else ("", "", dur)
    hours = 0.0
    try:
        if days:
            hours += float(days) * 24
        rest = rest.lstrip("T")
        if rest.endswith("H"):
            hours += float(rest[:-1])
        elif rest.endswith("M"):
            hours += float(rest[:-1]) / 60
    except ValueError:
        return 1.0
    return hours or 1.0


def _value_at(series: list[dict], at) -> float | None:
    """The value valid at `at`; the latest one before it if no interval
    covers it (a grid is refreshed hourly and can lag); the first otherwise."""
    if not series:
        return None
    latest_before = None
    for row in series:
        end = row["start"] + timedelta(hours=row["hours"])
        if row["start"] <= at < end and row["value"] is not None:
            return row["value"]
        if row["start"] <= at and row["value"] is not None:
            latest_before = row["value"]
    if latest_before is not None:
        return latest_before
    return next((r["value"] for r in series if r["value"] is not None), None)


def fetch_gridpoint(config: dict) -> dict:
    data = get_json(_grid_url(config))
    props = data.get("properties") or {}
    now = utc_now()
    thunder = _series(props, "probabilityOfThunder")
    precip = _series(props, "probabilityOfPrecipitation")
    hours_ahead = []
    horizon = now + timedelta(hours=24)
    for row in thunder:
        if row["start"] < now - timedelta(hours=1) or row["start"] > horizon:
            continue
        hours_ahead.append({"at": row["start"].isoformat(timespec="minutes"),
                            "thunder_pct": row["value"],
                            "precip_pct": _value_at(precip, row["start"])})
    payload = {
        "temp_f": c_to_f(_value_at(_series(props, "temperature"), now)),
        "feels_f": c_to_f(_value_at(_series(props, "apparentTemperature"), now)),
        "wind_mph": kmh_to_mph(_value_at(_series(props, "windSpeed"), now)),
        "gust_mph": kmh_to_mph(_value_at(_series(props, "windGust"), now)),
        "wind_dir": compass(_value_at(_series(props, "windDirection"), now)),
        "sky_pct": _int(_value_at(_series(props, "skyCover"), now)),
        "precip_pct": _int(_value_at(precip, now)),
        "thunder_pct": _int(_value_at(thunder, now)),
        "humidity_pct": _int(_value_at(_series(props, "relativeHumidity"), now)),
        "visibility_mi": m_to_mi(_value_at(_series(props, "visibility"), now)),
        "day_max_precip_pct": _int(max((r["value"] for r in precip
                                        if r["value"] is not None and now.date() == r["start"].date()),
                                       default=None)),
        "day_max_thunder_pct": _int(max((r["value"] for r in thunder
                                         if r["value"] is not None and now.date() == r["start"].date()),
                                        default=None)),
        "hours_ahead": hours_ahead,
        "grid_updated": props.get("updateTime"),
        "source": "NWS", "office": config.get("office"),
    }
    if payload["temp_f"] is None and payload["wind_mph"] is None:
        raise SourceError("nws gridpoint answered with no temperature or wind")
    return payload


def _int(value):
    return None if value is None else int(round(float(value)))


def fetch_forecast(config: dict) -> dict:
    data = get_json(_grid_url(config) + "/forecast")
    periods = (data.get("properties") or {}).get("periods") or []
    if not periods:
        raise SourceError("nws forecast answered with no periods")
    days: list[dict] = []
    pending = None
    for per in periods:
        temp = per.get("temperature")
        if per.get("temperatureUnit") == "C":
            temp = c_to_f(temp)
        pop = (per.get("probabilityOfPrecipitation") or {}).get("value")
        start = parse_iso(per.get("startTime"))
        row = {"name": per.get("name"), "date": start.date().isoformat() if start else None,
               "wind": per.get("windSpeed"), "wind_dir": per.get("windDirection"),
               "short": per.get("shortForecast"), "precip_pct": _int(pop),
               "icon": _icon_kind(per.get("shortForecast") or "")}
        if per.get("isDaytime"):
            pending = {**row, "high_f": temp, "low_f": None}
            days.append(pending)
        else:
            if pending is not None and pending.get("low_f") is None:
                pending["low_f"] = temp
                pending = None
            elif not days:
                # A forecast that starts tonight: carry the night as day one
                # so the page never opens with an empty column.
                days.append({**row, "high_f": None, "low_f": temp})
    return {"days": days[:7], "generated": (data.get("properties") or {}).get("generatedAt"),
            "office": config.get("office"), "source": "NWS"}


def _icon_kind(short: str) -> str:
    s = short.lower()
    if "thunder" in s:
        return "storm"
    if "snow" in s or "flurr" in s:
        return "snow"
    if "rain" in s or "shower" in s or "drizzle" in s:
        return "rain"
    if "fog" in s or "haze" in s:
        return "fog"
    if "mostly cloudy" in s or "cloudy" == s.strip():
        return "cloudy"
    if "partly" in s or "mostly sunny" in s:
        return "partly"
    if "sunny" in s or "clear" in s:
        return "sun"
    return "cloudy"


def fetch_alerts(config: dict) -> dict:
    seen: dict[str, dict] = {}
    queries = []
    if config.get("lat") is not None and config.get("lon") is not None:
        queries.append({"point": f"{float(config['lat']):.4f},{float(config['lon']):.4f}"})
    for zone in config.get("zones") or []:
        queries.append({"zone": zone})
    if not queries:
        raise SourceError("nws alerts: a point or a zone is required")
    for params in queries:
        data = get_json(f"{BASE}/alerts/active", params=params)
        for feat in data.get("features") or []:
            p = feat.get("properties") or {}
            key = p.get("id") or feat.get("id") or p.get("headline")
            if not key or key in seen:
                continue
            seen[key] = {
                "id": key, "event": p.get("event"), "severity": p.get("severity"),
                "urgency": p.get("urgency"), "headline": p.get("headline"),
                "onset": p.get("onset"), "ends": p.get("ends") or p.get("expires"),
                "area": p.get("areaDesc"), "url": (feat.get("id") if str(feat.get("id", "")).startswith("http") else None),
                "scope": "point" if "point" in params else params.get("zone"),
            }
    return {"alerts": list(seen.values()), "queried": queries, "source": "NWS"}


def fetch_station(config: dict) -> dict:
    station = config.get("station")
    if not station:
        raise SourceError("nws station: a station id is required")
    data = get_json(f"{BASE}/stations/{station}/observations/latest")
    p = data.get("properties") or {}

    def val(name):
        return (p.get(name) or {}).get("value")

    payload = {
        "temp_f": c_to_f(val("temperature")),
        "wind_mph": kmh_to_mph(val("windSpeed")),
        "gust_mph": kmh_to_mph(val("windGust")),
        "wind_dir": compass(val("windDirection")),
        "humidity_pct": _int(val("relativeHumidity")),
        "text": p.get("textDescription"),
        "observed_at": p.get("timestamp"),
        "station": station, "station_name": config.get("station_name"),
        "distance_mi": config.get("distance_mi"), "source": "NWS",
    }
    if payload["temp_f"] is None and payload["wind_mph"] is None:
        raise SourceError(f"nws station {station} answered with no observation")
    return payload
