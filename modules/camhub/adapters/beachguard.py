"""Ohio Department of Health BeachGuard -- publicapps.odh.ohio.gov. Free, no key.

**Undocumented.** The endpoints were recovered from the site's own JavaScript
and are not published for third-party use, so field names can change without
notice. Every read here goes through `_pick()`, which matches a handful of
spellings case-insensitively, and a failure raises -- so the store records an
error and the advisory bar disappears. It never shows a false all-clear.

Ohio-only, so this is a state-specific adapter; the probe answers "not
found" outside Ohio rather than calling a feed that cannot know the place.
"""
from __future__ import annotations

from .http import SourceError, get_json
from .units import haversine_mi, parse_iso, utc_now

BASE = "https://publicapps.odh.ohio.gov/beachguardpublic/api"
MAX_MI = 5.0


def _pick(row: dict, *names, default=None):
    if not isinstance(row, dict):
        return default
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lowered and lowered[name.lower()] not in (None, ""):
            return lowered[name.lower()]
    return default


def _rows(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "items", "results", "beaches", "advisories", "monitorings"):
            if isinstance(data.get(key), list):
                return data[key]
    raise SourceError("beachguard: unexpected response shape")


def beaches() -> list[dict]:
    out = []
    for row in _rows(get_json(f"{BASE}/beacheslist")):
        bid = _pick(row, "BeachId", "beachId", "id")
        lat, lon = _pick(row, "Latitude", "lat"), _pick(row, "Longitude", "lng", "lon")
        try:
            out.append({"id": int(bid), "name": _pick(row, "BeachName", "name", default=str(bid)),
                        "lat": float(lat), "lon": float(lon),
                        "waterbody": _pick(row, "WaterBody", "LakeName", "waterbody", default="")})
        except (TypeError, ValueError):
            continue
    return out


def probe(lat: float, lon: float, location_type: str = "inland_lake") -> list[dict]:
    if not (38.4 <= lat <= 42.0 and -84.9 <= lon <= -80.5):
        return [{"key": "advisories", "adapter": "beachguard", "label": "Beach advisories",
                 "found": False, "config": {}, "detail": "BeachGuard covers Ohio only"}]
    rows = []
    for beach in beaches():
        dist = haversine_mi(lat, lon, beach["lat"], beach["lon"])
        if dist <= MAX_MI:
            rows.append({"key": "advisories", "adapter": "beachguard",
                         "label": "ODH BeachGuard advisories", "found": True,
                         "distance_mi": dist, "cadence_minutes": 60,
                         "config": {"beach_id": beach["id"], "beach_name": beach["name"],
                                    "distance_mi": dist},
                         "detail": f"{beach['name']} (beach {beach['id']}), {dist} mi"})
    rows.sort(key=lambda r: r.get("distance_mi") or 0)
    if not rows:
        rows.append({"key": "advisories", "adapter": "beachguard", "label": "Beach advisories",
                     "found": False, "config": {},
                     "detail": f"no monitored beach within {MAX_MI:g} miles"})
    return rows


def fetch(config: dict) -> dict:
    beach_id = config.get("beach_id")
    if not beach_id:
        raise SourceError("beachguard: a beach id is required")
    now = utc_now()
    active = []
    for row in _rows(get_json(f"{BASE}/advisorieslist", params={"beachId": beach_id})):
        start = parse_iso(_pick(row, "StartDate", "AdvisoryStartDate", "start"))
        end = parse_iso(_pick(row, "EndDate", "AdvisoryEndDate", "ReopenDate", "end"))
        if start and start > now:
            continue
        if end and end < now:
            continue
        severity = _pick(row, "AdvisoryLevel", "Severity", "Level", default=0)
        try:
            severity = int(severity)
        except (TypeError, ValueError):
            severity = 0
        active.append({
            "type": _pick(row, "AdvisoryType", "Type", default="Advisory"),
            "severity": severity,
            "reason": _pick(row, "Reason", "AdvisoryReason", "Description", default=""),
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        })
    season = {"start": None, "end": None}
    try:
        for row in _rows(get_json(f"{BASE}/monitoringslist", params={"beachId": beach_id})):
            s = parse_iso(_pick(row, "SeasonStart", "StartDate", "MonitoringStartDate"))
            e = parse_iso(_pick(row, "SeasonEnd", "EndDate", "MonitoringEndDate"))
            if s and (season["start"] is None or s.date().isoformat() < season["start"]):
                season["start"] = s.date().isoformat()
            if e and (season["end"] is None or e.date().isoformat() > season["end"]):
                season["end"] = e.date().isoformat()
    except SourceError:
        pass  # the season window is a nicety; the advisories are the point
    in_season = True
    if season["start"] and season["end"]:
        today = now.date().isoformat()
        in_season = season["start"] <= today <= season["end"]
    return {"beach_id": int(beach_id), "beach_name": config.get("beach_name"),
            "distance_mi": config.get("distance_mi"), "active": active,
            "swim_season": season, "in_season": in_season, "source": "ODH BeachGuard",
            "url": "https://publicapps.odh.ohio.gov/BeachGuardPublic/"}
