"""NOAA NDBC buoys -- ndbc.noaa.gov. Free, no key.

Not useful at Buckeye Lake (no buoy), and built anyway: this is what lights
up the water-temperature and wave tiles for a coastal or Great Lakes client.
The realtime feed is a whitespace table with a two-line header; MM marks a
missing value.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .http import SourceError, get_text
from .units import c_to_f, compass, haversine_mi, m_to_ft

MAX_MI = 25.0
STATIONS_URL = "https://www.ndbc.noaa.gov/activestations.xml"


def stations() -> list[dict]:
    text = get_text(STATIONS_URL)
    out = []
    for m in re.finditer(r"<station\s+([^>]*)/?>", text):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        try:
            out.append({"id": attrs.get("id"), "lat": float(attrs["lat"]),
                        "lon": float(attrs["lon"]), "name": attrs.get("name", ""),
                        "type": attrs.get("type", "")})
        except (KeyError, ValueError):
            continue
    return out


def probe(lat: float, lon: float, location_type: str = "inland_lake") -> list[dict]:
    if location_type in ("inland_lake", "golf", "ski", "town", "river"):
        return [{"key": "water", "adapter": "ndbc", "label": "NOAA buoy", "found": False,
                 "config": {}, "detail": "buoys are for coastal and Great Lakes cams"}]
    best = None
    for st in stations():
        dist = haversine_mi(lat, lon, st["lat"], st["lon"])
        if dist <= MAX_MI and (best is None or dist < best["distance_mi"]):
            best = {**st, "distance_mi": dist}
    if not best:
        return [{"key": "water", "adapter": "ndbc", "label": "NOAA buoy", "found": False,
                 "config": {}, "detail": f"no buoy within {MAX_MI:g} miles"}]
    return [{"key": "water", "adapter": "ndbc", "label": "NOAA NDBC buoy", "found": True,
             "distance_mi": best["distance_mi"], "cadence_minutes": 30,
             "config": {"station": best["id"], "station_name": best["name"],
                        "distance_mi": best["distance_mi"]},
             "detail": f"{best['name']} ({best['id']}), {best['distance_mi']} mi"}]


def parse_realtime(text: str) -> dict:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3 or not lines[0].startswith("#"):
        raise SourceError("ndbc: unexpected realtime table")
    headers = lines[0].lstrip("#").split()
    row = lines[2].split()
    if len(row) < len(headers):
        raise SourceError("ndbc: short data row")
    rec = dict(zip(headers, row))

    def num(name):
        v = rec.get(name)
        if v in (None, "MM", ""):
            return None
        try:
            return float(v)
        except ValueError:
            return None

    try:
        observed = datetime(int(rec["YY"]), int(rec["MM"]), int(rec["DD"]),
                            int(rec["hh"]), int(rec["mm"]), tzinfo=timezone.utc)
    except (KeyError, ValueError):
        observed = None
    wspd, gst = num("WSPD"), num("GST")
    return {
        "water_temp_f": c_to_f(num("WTMP")), "air_temp_f": c_to_f(num("ATMP")),
        "wave_height_ft": m_to_ft(num("WVHT")), "wave_period_s": num("DPD"),
        "wind_mph": round(wspd * 2.23694) if wspd is not None else None,
        "gust_mph": round(gst * 2.23694) if gst is not None else None,
        "wind_dir": compass(num("WDIR")),
        "observed_at": observed.isoformat() if observed else None,
    }


def fetch(config: dict) -> dict:
    station = config.get("station")
    if not station:
        raise SourceError("ndbc: a station id is required")
    payload = parse_realtime(get_text(f"https://www.ndbc.noaa.gov/data/realtime2/{station}.txt"))
    if payload["water_temp_f"] is None and payload["wave_height_ft"] is None:
        raise SourceError(f"ndbc {station} reports neither water temperature nor waves")
    return {**payload, "station": station, "station_name": config.get("station_name"),
            "distance_mi": config.get("distance_mi"), "source": "NOAA NDBC"}
