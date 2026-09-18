"""Unit and time helpers the adapters share. Fahrenheit and mph on the page,
whatever the provider sends."""
from __future__ import annotations

import math
from datetime import datetime, timezone


def c_to_f(value):
    if value is None:
        return None
    return round(float(value) * 9 / 5 + 32)


def kmh_to_mph(value):
    if value is None:
        return None
    return round(float(value) * 0.621371)


def m_to_mi(value):
    if value is None:
        return None
    return round(float(value) / 1609.344, 1)


def m_to_ft(value):
    if value is None:
        return None
    return round(float(value) * 3.28084, 1)


def haversine_mi(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


def parse_iso(value):
    """An aware datetime from an ISO string, or None. NWS writes offsets as
    +00:00; a trailing Z is accepted too."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def compass(degrees):
    if degrees is None:
        return ""
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[int((float(degrees) + 22.5) // 45) % 8]
