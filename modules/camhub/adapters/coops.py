"""NOAA CO-OPS tides -- api.tidesandcurrents.noaa.gov. Free, no key.

Nothing at Buckeye Lake (no tide station); the adapter is here so a coastal
client's tide tile is a configuration exercise. Predictions are the next
high and low; water temperature comes from the same station where it
reports it.
"""
from __future__ import annotations

from .http import SourceError, get_json
from .units import haversine_mi, parse_iso, utc_now

MAX_MI = 25.0
API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
META = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"


def probe(lat: float, lon: float, location_type: str = "inland_lake") -> list[dict]:
    if location_type != "coastal":
        return [{"key": "tides", "adapter": "coops", "label": "NOAA tide station",
                 "found": False, "config": {}, "detail": "tides are for coastal cams"}]
    data = get_json(META, params={"type": "waterlevels"})
    best = None
    for st in data.get("stations") or []:
        try:
            dist = haversine_mi(lat, lon, float(st["lat"]), float(st["lng"]))
        except (KeyError, TypeError, ValueError):
            continue
        if dist <= MAX_MI and (best is None or dist < best["distance_mi"]):
            best = {"id": st.get("id"), "name": st.get("name"), "distance_mi": dist}
    if not best:
        return [{"key": "tides", "adapter": "coops", "label": "NOAA tide station",
                 "found": False, "config": {}, "detail": f"no tide station within {MAX_MI:g} miles"}]
    return [{"key": "tides", "adapter": "coops", "label": "NOAA CO-OPS tides", "found": True,
             "distance_mi": best["distance_mi"], "cadence_minutes": 60,
             "config": {"station": best["id"], "station_name": best["name"],
                        "distance_mi": best["distance_mi"]},
             "detail": f"{best['name']} ({best['id']}), {best['distance_mi']} mi"}]


def fetch(config: dict) -> dict:
    station = config.get("station")
    if not station:
        raise SourceError("coops: a station id is required")
    common = {"station": station, "units": "english", "time_zone": "lst_ldt",
              "format": "json", "application": "SmartHub-CamModule"}
    data = get_json(API, params={**common, "product": "predictions", "datum": "MLLW",
                                 "interval": "hilo", "date": "today"})
    preds = data.get("predictions") or []
    if not preds:
        raise SourceError(f"coops {station}: no predictions")
    now_local = utc_now()
    upcoming = []
    for p in preds:
        when = parse_iso(str(p.get("t", "")).replace(" ", "T"))
        upcoming.append({"type": "high" if p.get("type") == "H" else "low",
                         "time": p.get("t"), "height_ft": _f(p.get("v")),
                         "_when": when})
    upcoming.sort(key=lambda r: r["time"] or "")
    nxt = next((r for r in upcoming if r["_when"] and r["_when"].replace(tzinfo=None) >= now_local.replace(tzinfo=None)), None)
    for r in upcoming:
        r.pop("_when", None)
    payload = {"station": station, "station_name": config.get("station_name"),
               "distance_mi": config.get("distance_mi"), "tides": upcoming,
               "next_tide": nxt, "source": "NOAA CO-OPS"}
    try:
        wt = get_json(API, params={**common, "product": "water_temperature", "date": "latest"})
        rows = wt.get("data") or []
        if rows and rows[-1].get("v") not in (None, ""):
            payload["water_temp_f"] = round(float(rows[-1]["v"]))
            payload["water_observed_at"] = rows[-1].get("t")
    except (SourceError, ValueError, TypeError):
        pass
    return payload


def _f(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None
