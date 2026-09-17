"""USGS Water Data -- api.waterdata.usgs.gov/ogcapi/v0. Free, no key.

Built on the OGC API and **not** on waterservices.usgs.gov: USGS has
announced the legacy WaterServices API is decommissioned in Q1 2027 with
degradation possible from August 2026, which is now.

Two rules the probe needs, both learned at Buckeye Lake. **Filter by
distance and site type** -- a bounding box around the lake returns 45 gauges
including groundwater wells and streams 11 miles away; only lake-type sites
(`LK`) within a few miles are candidates for a lake-level tile, and stream
sites (`ST`) are what a river page wants. And **verify, do not trust
metadata** -- two Buckeye Lake sites look real in the catalogue and return
nothing at all, so a candidate is kept only when `latest-continuous` answers
with a value and a recent timestamp.

Parameter codes: 62614 lake elevation (ft, NGVD29 at Buckeye), 00065 gauge
height, 00060 streamflow (cfs), 00010 water temperature (C), 00045 precip.
"""
from __future__ import annotations

from .http import SourceError, get_json
from .units import c_to_f, haversine_mi, parse_iso, utc_now

BASE = "https://api.waterdata.usgs.gov/ogcapi/v0"
PARAMS = {
    "62614": ("lake_level", "Lake surface elevation", "ft"),
    "00065": ("gauge_height", "Gauge height", "ft"),
    "00060": ("streamflow", "Streamflow", "cfs"),
    "00010": ("water_temp", "Water temperature", "C"),
    "00045": ("rainfall", "Precipitation", "in"),
}
# Site types worth a tile, by what the cam looks at.
SITE_TYPES = {
    "inland_lake": ("LK",), "great_lakes": ("LK", "ST"), "river": ("ST",),
    "coastal": ("ST", "ES"), "default": ("LK", "ST"),
}
MAX_MI = 8.0


def _site_id(text) -> str:
    text = str(text or "")
    return text if text.startswith("USGS-") else f"USGS-{text}" if text else ""


def latest(site: str, parameter: str) -> dict | None:
    """The latest continuous value for one site and parameter, or None when
    the site reports nothing for it."""
    data = get_json(f"{BASE}/collections/latest-continuous/items",
                    params={"monitoring_location_id": _site_id(site),
                            "parameter_code": parameter, "f": "json"})
    for feat in data.get("features") or []:
        p = feat.get("properties") or {}
        if p.get("value") is None:
            continue
        return {"value": float(p["value"]), "time": p.get("time"),
                "unit": p.get("unit_of_measure"),
                "approval": p.get("approval_status"),
                "statistic": p.get("statistic_id")}
    return None


def probe(lat: float, lon: float, location_type: str = "inland_lake") -> list[dict]:
    box = 0.12
    data = get_json(f"{BASE}/collections/monitoring-locations/items",
                    params={"bbox": f"{lon - box},{lat - box},{lon + box},{lat + box}",
                            "f": "json", "limit": 200})
    wanted = SITE_TYPES.get(location_type, SITE_TYPES["default"])
    rows: list[dict] = []
    for feat in data.get("features") or []:
        p = feat.get("properties") or {}
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        site_type = p.get("site_type_code") or p.get("site_type")
        if site_type not in wanted:
            continue
        dist = haversine_mi(lat, lon, coords[1], coords[0])
        if dist > MAX_MI:
            continue
        number = p.get("monitoring_location_number") or str(feat.get("id") or "").replace("USGS-", "")
        name = p.get("monitoring_location_name") or number
        for code, (key, label, unit) in PARAMS.items():
            try:
                value = latest(number, code)
            except SourceError as exc:
                rows.append({"key": key, "adapter": "usgs", "label": f"USGS {label}",
                             "found": False, "config": {}, "distance_mi": dist,
                             "detail": f"{name}: {exc}"})
                continue
            if not value:
                continue
            when = parse_iso(value.get("time"))
            age_h = (utc_now() - when).total_seconds() / 3600 if when else None
            if age_h is None or age_h > 24 * 7:
                continue
            rows.append({"key": key, "adapter": "usgs", "label": f"USGS {label}",
                         "found": True, "distance_mi": dist, "cadence_minutes": 15,
                         "config": {"site": number, "site_name": name, "parameter": code,
                                    "site_type": site_type, "lat": coords[1], "lon": coords[0],
                                    "distance_mi": dist},
                         "detail": f"{name} ({number}) {value['value']} {unit}, "
                                   f"{value.get('time')}, {dist} mi"})
    if not any(r["found"] for r in rows):
        rows.append({"key": "lake_level", "adapter": "usgs", "label": "USGS gauge",
                     "found": False, "config": {},
                     "detail": f"no reporting {'/'.join(wanted)} site within {MAX_MI:g} miles"})
    return rows


def fetch(config: dict) -> dict:
    site, parameter = config.get("site"), str(config.get("parameter") or "62614")
    if not site:
        raise SourceError("usgs: a site number is required")
    value = latest(site, parameter)
    if not value:
        raise SourceError(f"usgs {site} reports nothing for parameter {parameter}")
    key = PARAMS.get(parameter, ("value", "", ""))[0]
    payload = {
        "site": str(site), "site_name": config.get("site_name"),
        "parameter": parameter, "observed_at": value.get("time"),
        "provisional": (value.get("approval") or "").lower() != "approved",
        "unit": value.get("unit"), "distance_mi": config.get("distance_mi"),
        "source": "USGS",
    }
    if parameter == "62614":
        payload["elevation_ft"] = round(value["value"], 2)
    elif parameter == "00065":
        payload["gauge_height_ft"] = round(value["value"], 2)
    elif parameter == "00060":
        payload["streamflow_cfs"] = round(value["value"])
    elif parameter == "00010":
        payload["water_temp_f"] = c_to_f(value["value"])
    else:
        payload[key] = value["value"]
    return payload
