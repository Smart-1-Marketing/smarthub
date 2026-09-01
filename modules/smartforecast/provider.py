"""WeatherAPI adapter with a normalized SmartForecast snapshot shape."""
from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class WeatherProviderError(RuntimeError):
    pass


def configured() -> bool:
    return bool((os.environ.get("WEATHERAPI_KEY") or "").strip())


def fetch_weather(postal_code: str, timeout: int = 12) -> dict:
    key = (os.environ.get("WEATHERAPI_KEY") or "").strip()
    if not key:
        raise WeatherProviderError("WEATHERAPI_KEY is not configured; cached weather remains available.")
    query = urlencode({"key": key, "q": postal_code, "days": 5, "alerts": "yes", "aqi": "no"})
    request = Request("https://api.weatherapi.com/v1/forecast.json?" + query,
                      headers={"User-Agent": "Smart1-SmartForecast/0.1"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 — fixed provider host
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — provider errors become a plain UI status
        raise WeatherProviderError(f"WeatherAPI request failed: {type(exc).__name__}") from exc
    return normalize_weatherapi(payload)


def normalize_weatherapi(payload: dict) -> dict:
    current = payload.get("current") or {}
    days = (payload.get("forecast") or {}).get("forecastday") or []
    today = (days[0].get("day") if days else {}) or {}
    alerts = (payload.get("alerts") or {}).get("alert") or []
    snow = 0.0
    rain = float(today.get("daily_chance_of_rain") or 0)
    wind = float(current.get("wind_mph") or today.get("maxwind_mph") or 0)
    weekend_days = []
    sustained_heat_days = 0
    for day in days[:3]:
        detail = day.get("day") or {}
        snow = max(snow, float(detail.get("totalsnow_cm") or 0) / 2.54)
        rain = max(rain, float(detail.get("daily_chance_of_rain") or 0))
        wind = max(wind, float(detail.get("maxwind_mph") or 0))
        sustained_heat_days += float(detail.get("maxtemp_f") or 0) >= 88
    for day in days:
        try:
            is_weekend = datetime.fromisoformat(str(day.get("date"))).weekday() >= 5
        except (TypeError, ValueError):
            is_weekend = False
        if is_weekend:
            weekend_days.append(day.get("day") or {})
    weekend_high = max((float(item.get("maxtemp_f") or 0) for item in weekend_days), default=0)
    weekend_rain = max((float(item.get("daily_chance_of_rain") or 0) for item in weekend_days), default=100)
    weekend_wind = max((float(item.get("maxwind_mph") or 0) for item in weekend_days), default=100)
    return {
        "temperature": float(current.get("temp_f") or 0),
        "feels_like": float(current.get("feelslike_f") or current.get("temp_f") or 0),
        "humidity": float(current.get("humidity") or 0),
        "dew_point": float(current.get("dewpoint_f") or 0),
        "forecast_high": float(today.get("maxtemp_f") or current.get("temp_f") or 0),
        "forecast_low": float(today.get("mintemp_f") or current.get("temp_f") or 0),
        "rain_probability": rain,
        "snow_inches": round(snow, 2),
        "wind_mph": wind,
        "sustained_heat_days": sustained_heat_days,
        "weekend_high": weekend_high,
        "weekend_rain_probability": weekend_rain,
        "weekend_wind_mph": weekend_wind,
        "hours_until_event": 0,
        "official_alerts": [str(item.get("headline") or item.get("event") or "")
                            for item in alerts if item.get("headline") or item.get("event")],
        "location": (payload.get("location") or {}).get("name", ""),
    }
