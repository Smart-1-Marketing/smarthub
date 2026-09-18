"""Sunrise, sunset, civil twilight, day length, moon phase -- computed.

Deterministic from latitude, longitude and date, so no API is called: an API
for arithmetic adds a dependency, a failure mode and a rate limit. The solar
part is the NOAA solar position algorithm; the moon is the synodic-month
approximation, which is within a day and plenty for a phase name.

`probe` always answers found; `fetch` never fails.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SYNODIC_DAYS = 29.530588853
# A known new moon: 2000-01-06 18:14 UTC.
NEW_MOON_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
PHASES = ("New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
          "Full moon", "Waning gibbous", "Last quarter", "Waning crescent")


def _julian_day(dt: datetime) -> float:
    dt = dt.astimezone(timezone.utc)
    y, m = dt.year, dt.month
    d = dt.day + (dt.hour + dt.minute / 60 + dt.second / 3600) / 24
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def _sun_event(day: date, lat: float, lon: float, tz: ZoneInfo, *, rising: bool,
               zenith: float = 90.833) -> datetime | None:
    """NOAA's sunrise/sunset from the equation of time and declination, at
    local solar noon for the day, iterated once for the event hour."""
    noon_utc = datetime(day.year, day.month, day.day, 12, tzinfo=tz).astimezone(timezone.utc)
    jd = _julian_day(noon_utc)
    for _ in range(2):
        t = (jd - 2451545.0) / 36525.0
        l0 = (280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360
        m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
        mr = math.radians(m)
        c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
             + math.sin(2 * mr) * (0.019993 - 0.000101 * t) + math.sin(3 * mr) * 0.000289)
        true_long = l0 + c
        omega = 125.04 - 1934.136 * t
        app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
        e0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
        obliq = e0 + 0.00256 * math.cos(math.radians(omega))
        decl = math.degrees(math.asin(math.sin(math.radians(obliq)) * math.sin(math.radians(app_long))))
        y = math.tan(math.radians(obliq / 2)) ** 2
        ecc = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
        eqt = 4 * math.degrees(
            y * math.sin(2 * math.radians(l0)) - 2 * ecc * math.sin(mr)
            + 4 * ecc * y * math.sin(mr) * math.cos(2 * math.radians(l0))
            - 0.5 * y * y * math.sin(4 * math.radians(l0)) - 1.25 * ecc * ecc * math.sin(2 * mr))
        cos_ha = ((math.cos(math.radians(zenith)) / (math.cos(math.radians(lat)) * math.cos(math.radians(decl))))
                  - math.tan(math.radians(lat)) * math.tan(math.radians(decl)))
        if cos_ha < -1 or cos_ha > 1:
            return None  # polar day or night
        ha = math.degrees(math.acos(cos_ha))
        minutes = 720 - 4 * (lon + (ha if rising else -ha)) - eqt
        event = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(minutes=minutes)
        jd = _julian_day(event)
    return event.astimezone(tz)


def moon(dt: datetime) -> dict:
    age = ((dt.astimezone(timezone.utc) - NEW_MOON_EPOCH).total_seconds() / 86400) % SYNODIC_DAYS
    frac = age / SYNODIC_DAYS
    illumination = round((1 - math.cos(2 * math.pi * frac)) / 2 * 100)
    phase = PHASES[int((frac * 8 + 0.5) % 8)]
    return {"moon_age_days": round(age, 1), "moon_phase": phase,
            "moon_illumination_pct": illumination}


def compute(lat: float, lon: float, tz_name: str, day: date | None = None) -> dict:
    tz = ZoneInfo(tz_name or "UTC")
    now = datetime.now(tz)
    day = day or now.date()
    sunrise = _sun_event(day, lat, lon, tz, rising=True)
    sunset = _sun_event(day, lat, lon, tz, rising=False)
    dawn = _sun_event(day, lat, lon, tz, rising=True, zenith=96.0)
    dusk = _sun_event(day, lat, lon, tz, rising=False, zenith=96.0)
    fmt = lambda d: d.isoformat(timespec="minutes") if d else None  # noqa: E731
    length = round((sunset - sunrise).total_seconds() / 60) if sunrise and sunset else None
    return {"date": day.isoformat(), "timezone": tz_name,
            "sunrise": fmt(sunrise), "sunset": fmt(sunset),
            "civil_dawn": fmt(dawn), "civil_dusk": fmt(dusk),
            "day_length_min": length, **moon(now), "source": "computed"}


def probe(lat: float, lon: float, location_type: str = "inland_lake") -> list[dict]:
    return [{"key": "astronomy", "adapter": "astro", "label": "Sunrise, sunset, moon (computed)",
             "found": True, "cadence_minutes": 360,
             "config": {"lat": lat, "lon": lon},
             "detail": "always available; nothing to call"}]


def fetch(config: dict) -> dict:
    return compute(float(config["lat"]), float(config["lon"]),
                   config.get("timezone") or "America/New_York")
